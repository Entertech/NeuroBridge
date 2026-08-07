#include "Pleasure.h"
#include "Device.h"
#include "Data.h"

using namespace basic;
using namespace affection;
namespace ac
{
    PleasureComputing::PleasureComputing()
    {
        temp.index = 0;
        temp.wearFlagStore = nc::zeros<double>(1, 15);
        temp.pleasureTmp.weight = 0.2;
        nc::NdArray<double> features1 = {};
        nc::NdArray<double> features2 = {};
        temp.pleasureTmp.featuresStore.clear();
        temp.pleasureTmp.featuresStore.push_back(features1);
        temp.pleasureTmp.featuresStore.push_back(features2);
        temp.pleasureTmp.rulers = params::pleasureRuler;
    }

    PleasureComputing::~PleasureComputing()
    {
    }

    double PleasureComputing::trigger(const basic::SessionCache &cache)
    {
        auto eeglPower = cache.eeglPower;
        auto eegrPower = cache.eegrPower;
        auto eegQuality = cache.eegQuality;

        //算法执行
        // --佩戴标志记录
        if (eegQuality != dsp::EEGQuality::NONE)
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1, temp.wearFlagStore.size())], {1.0}});
        else
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1, temp.wearFlagStore.size())], {0.}});

        // ---佩戴判断
        //---佩戴判断
        if (nc::sum(temp.wearFlagStore[nc::Slice(temp.wearFlagStore.size() - 15, temp.wearFlagStore.size())]).item() == 0)
        {
            temp.wearFlagStore = nc::zeros<double>(1, 15);
            temp.pleasureTmp.weight = 0.2;
            nc::NdArray<double> features1  = nc::NdArray<double>();
            nc::NdArray<double> features2  = nc::NdArray<double>();
            temp.pleasureTmp.featuresStore.clear();
            temp.pleasureTmp.featuresStore.push_back(features1);
            temp.pleasureTmp.featuresStore.push_back(features2);
            temp.pleasureTmp.rulers = params::pleasureRuler;
            temp.pleasureTmp.pleasureStore  = nc::NdArray<double>();
        }
        // --计算愉悦度
        auto pleasure = handler::pleasureHandler(
            eeglPower.alphaNorm(),
            eegrPower.alphaNorm(),
            eeglPower.thetaNorm(),
            eegrPower.thetaNorm(),
            temp.pleasureTmp,
            20,
            100,
            20,
            0.5,
            50.);
        temp.index += 1;

        temp.pleasureRec.push_back(pleasure);

        return nc::round(pleasure, 2);
    }

    PleasureReportRes PleasureComputing::report()
    {
        PleasureReportRes res;
        res.pleasureAvg = 0;
        if (temp.pleasureRec.size() < 1)
            return res;

        auto pleasureNdArray = nc::NdArray<double>(temp.pleasureRec);
        res.pleasureRec = nc::round(pleasureNdArray, 2).toStlVector();
        auto pleasureMean = nc::mean(pleasureNdArray);
        res.pleasureAvg = double(nc::round(pleasureMean, 2).toStlVector().at(0));

        return res;
    }
}