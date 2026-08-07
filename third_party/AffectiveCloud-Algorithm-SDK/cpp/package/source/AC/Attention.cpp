#include "Attention.h"
#include "Device.h"
#include "Data.h"

using namespace basic;
using namespace affection;
namespace ac
{
    AttentionComputing::AttentionComputing()
    {
        temp.index = 0;
        temp.wearFlagStore = nc::zeros<double>(1, 15);
        temp.attentionTmp.weight = 0.5;
        nc::NdArray<double> features1 = {};
        nc::NdArray<double> features2 = {};
        nc::NdArray<double> features3 = {};
        temp.attentionTmp.featuresStore.clear();
        temp.attentionTmp.featuresStore.push_back(features1);
        temp.attentionTmp.featuresStore.push_back(features2);
        temp.attentionTmp.featuresStore.push_back(features3);
        temp.attentionTmp.rulers = params::attentionRuler;
    }

    AttentionComputing::~AttentionComputing()
    {
    }

    double AttentionComputing::trigger(const basic::SessionCache &cache)
    {
        auto eegQuality = cache.eegQuality;
        auto eegPower = cache.eegPower;

        //算法执行
        // --佩戴标志记录
        if (eegQuality != dsp::EEGQuality::NONE)
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1,temp.wearFlagStore.size())], {1.}});
        else
            temp.wearFlagStore = nc::hstack({temp.wearFlagStore[nc::Slice(1,temp.wearFlagStore.size())], {0.}});

        //---佩戴判断
        if (nc::sum(temp.wearFlagStore[nc::Slice(temp.wearFlagStore.size()-15,temp.wearFlagStore.size())]).item() == 0)
        {
            temp.wearFlagStore = nc::zeros<double>(1, 15);
            temp.attentionTmp.weight = 0.5;
            nc::NdArray<double> features1 = {};
            nc::NdArray<double> features2 = {};
            nc::NdArray<double> features3 = {};
            temp.attentionTmp.featuresStore.clear();
            temp.attentionTmp.featuresStore.push_back(features1);
            temp.attentionTmp.featuresStore.push_back(features2);
            temp.attentionTmp.featuresStore.push_back(features3);
            temp.attentionTmp.attentionStore = {};
            temp.attentionTmp.rulers = params::attentionRuler;
        }
        //计算注意力
        auto attention = handler::attentionHandler(
            eegPower.betaNorm(),
            eegPower.thetaNorm(),
            eegPower.gammaNorm(),
            eegPower.alphaNorm(),
            temp.attentionTmp,
            10,
            100,
            20,
            0.4,
            65
        );
        temp.index += 1;

        temp.attentionRec.push_back(attention);

        return nc::round(attention, 2);
    }

    AttentionReportRes AttentionComputing::report()
    {
        AttentionReportRes res;
        res.attentionAvg = 0;
        if (temp.attentionRec.size() < 1)
            return res;

        auto attentionNdArray = nc::NdArray<double>(temp.attentionRec);
        res.attentionRec = nc::round(attentionNdArray, 2).toStlVector();
        auto attentionMean = nc::mean(attentionNdArray);
        res.attentionAvg = double(nc::round(attentionMean, 2).toStlVector().at(0));

        return res;
    }
}