#include "Pressure.h"
#include "Device.h"
#include "Data.h"

using namespace basic;
using namespace affection;
namespace ac
{
    PressureComputing::PressureComputing()
    {
        temp.index = 0;
        temp.wearFlagStore = nc::zeros<double>(1,15);
    }

    PressureComputing::~PressureComputing()
    {
    }

    double PressureComputing::trigger(const basic::SessionCache &cache)
    {
        //读取外部缓存
        auto hr = cache.hr;
        auto hrv = cache.hrv;
        auto hrQuality = cache.hrQuality;
        auto hrPower = cache.hrPower;

        //算法执行
        //--佩戴标志记录
        if (hrQuality != dsp::HRQuality::INVALID)
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1,temp.wearFlagStore.size())], {1}});
        else
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1,temp.wearFlagStore.size())], {0}});

        //---佩戴判断
        if (nc::sum(temp.wearFlagStore[nc::Slice(temp.wearFlagStore.size()-15,temp.wearFlagStore.size())]).item() == 0)
        {
            temp.wearFlagStore = nc::zeros<double>(1,15);
            temp.pressureTmp.hrFreqRateStore = nc::NdArray<double>();
            temp.pressureTmp.pressureStore  = nc::NdArray<double>();
        }

        auto pressure = handler::pressureHandler(
            hr,
            hrv,
            hrPower.lf,
            hrPower.freqRate(),
            temp.pressureTmp,
            10
        );
        
        temp.index += 1;

        temp.pressureRec.push_back(pressure);

        return nc::round(pressure, 2);
    }

    PressureReportRes PressureComputing::report()
    {
        PressureReportRes res;
        res.pressureAvg = 0;
        if (temp.pressureRec.size() < 1)
            return res;

        auto pressureNdArray = nc::NdArray<double>(temp.pressureRec);
        res.pressureRec = nc::round(pressureNdArray, 2).toStlVector();
        auto pressureMean = nc::mean(pressureNdArray);
        res.pressureAvg = double(nc::round(pressureMean, 2).toStlVector().at(0));

        return res;
    }
}