#include "Arousal.h"
#include "Device.h"
#include "Data.h"

using namespace basic;
using namespace affection;
namespace ac
{
    ArousalComputing::ArousalComputing()
    {
        temp.index = 0;
        temp.arousalTmp.weight = 0.2;
        temp.wearFlagStore = nc::zeros<double>(1, 15);
    }

    ArousalComputing::~ArousalComputing()
    {
    }

    double ArousalComputing::trigger(const basic::SessionCache &cache)
    {
        // auto hrv = cache.hrv;
        auto hr = cache.hr;
        auto hrQuality = cache.hrQuality;
        auto hrPower = cache.hrPower;

        //算法执行
        //--佩戴标志记录
        if (hrQuality != dsp::HRQuality::INVALID)
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1, temp.wearFlagStore.size())], {1}});
        else
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1, temp.wearFlagStore.size())], {0}});
        //---佩戴判断
        if (nc::sum(temp.wearFlagStore[nc::Slice(temp.wearFlagStore.size() - 15, temp.wearFlagStore.size())]).item() == 0)
        {
            temp.wearFlagStore = nc::zeros<double>(1, 15);
            temp.arousalTmp.arousalStore = nc::NdArray<double>();
            temp.arousalTmp.hrFreqRateStore = nc::NdArray<double>();
            temp.arousalTmp.weight = 0.2;
        }
        //--计算激活度
        auto freq = hrPower.freqRate();
        auto arousal = handler::arousalHandler(
            hr,
            freq,
            temp.arousalTmp,
            20,
            50);

        temp.index += 1;

        temp.arousalRec.push_back(arousal);

        return nc::round(arousal, 2);
    }

    ArousalReportRes ArousalComputing::report()
    {
        ArousalReportRes res;
        res.arousalAvg = 0;
        if (temp.arousalRec.size() < 1)
            return res;

        auto arousalNdArray = nc::NdArray<double>(temp.arousalRec);
        res.arousalRec = nc::round(arousalNdArray, 2).toStlVector();
        auto arousalMean = nc::mean(arousalNdArray);
        res.arousalAvg = double(nc::round(arousalMean, 2).toStlVector().at(0));

        return res;
    }
}